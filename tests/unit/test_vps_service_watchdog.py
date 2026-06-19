"""Tests for the cause-aware service watchdog: restart rate-limit/back-off
(scripts/vps_service_watchdog.sh) and the restart notifier payload builder
(scripts/watchdog_restart_notifier.py)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = REPO_ROOT / "scripts" / "vps_service_watchdog.sh"


def _fake_systemctl(tmp_path: Path, restart_log: Path) -> Path:
    """A stand-in `systemctl` that reports the service inactive (forcing a
    restart attempt every cycle) and appends each restart to restart_log."""
    fake = tmp_path / "fake_systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  is-active) echo inactive; exit 0;;\n"
        '  is-enabled) echo "${ENABLED_STATE:-enabled}"; exit 0;;\n'
        '  restart) echo "$2" >> "$RESTART_LOG"; exit 0;;\n'
        "  reset-failed) exit 0;;\n"
        "  *) exit 0;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _run_cycle(tmp_path: Path, state_dir: Path, fake_systemctl: Path, restart_log: Path, max_per_hour: int, enabled_state: str = "enabled"):
    env = dict(os.environ)
    env.update(
        {
            "UA_WATCHDOG_SYSTEMCTL_BIN": str(fake_systemctl),
            "UA_WATCHDOG_STATE_DIR": str(state_dir),
            "UA_WATCHDOG_SERVICE_SPECS": "fakesvc||",
            "UA_WATCHDOG_NOTIFY_ENABLED": "0",
            "UA_WATCHDOG_POST_RESTART_SETTLE_SECONDS": "0",
            "UA_WATCHDOG_MAX_RESTARTS_PER_HOUR": str(max_per_hour),
            "RESTART_LOG": str(restart_log),
            "ENABLED_STATE": enabled_state,
        }
    )
    return subprocess.run(
        ["bash", str(WATCHDOG)], env=env, capture_output=True, text=True, timeout=60
    )


def test_watchdog_skips_disabled_unit(tmp_path):
    """A disabled unit (e.g. the autonomous-runtime worker after a split
    rollback) is left alone — never auto-restarted, even though it's inactive."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    restart_log.write_text("", encoding="utf-8")
    fake = _fake_systemctl(tmp_path, restart_log)

    proc = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour=6, enabled_state="disabled")
    assert proc.returncode == 0, proc.stderr
    restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert restarts == [], f"disabled unit must NOT be restarted, got {restarts}"
    assert "state=skipped reason=is-enabled:disabled" in proc.stdout

    # Sanity: the SAME inactive unit, when enabled, IS restarted (skip is the diff).
    proc2 = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour=6, enabled_state="enabled")
    assert proc2.returncode == 0, proc2.stderr
    restarts2 = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert restarts2 == ["fakesvc"], f"enabled inactive unit must be restarted, got {restarts2}"


def test_watchdog_rate_limits_restarts_and_backs_off(tmp_path):
    """A perpetually-inactive service is restarted up to the cap, then the
    watchdog backs off instead of flap-restarting every cycle forever."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    restart_log.write_text("", encoding="utf-8")
    fake = _fake_systemctl(tmp_path, restart_log)
    max_per_hour = 3

    # Run more cycles than the cap; each cycle sees the service inactive.
    for _ in range(max_per_hour + 3):
        proc = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour)
        assert proc.returncode == 0, proc.stderr

    restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    # Restarts are capped at max_per_hour; subsequent cycles back off.
    assert len(restarts) == max_per_hour, f"expected {max_per_hour} restarts, got {len(restarts)}"

    # The ledger persists exactly the capped number of restart timestamps.
    ledger = state_dir / "fakesvc.restarts"
    assert ledger.exists()
    stamps = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(stamps) == max_per_hour

    # The back-off path emits a flapping skip log on the over-cap cycles.
    last = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour)
    assert "action=skip_restart reason=flapping" in last.stdout


def _load_notifier():
    path = REPO_ROOT / "scripts" / "watchdog_restart_notifier.py"
    spec = importlib.util.spec_from_file_location("watchdog_restart_notifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ns(**kw):
    import argparse

    defaults = dict(
        service="universal-agent-gateway",
        reason="inactive:failed",
        event="restart",
        post_state="active",
        restart_count=2,
        window_seconds=3600,
        max_per_hour=6,
        escalated=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def test_notifier_payload_normal_restart_is_warning():
    module = _load_notifier()
    payload = module._build_payload(_ns(event="restart", escalated=False))
    assert payload["kind"] == "service_watchdog_restart"
    assert payload["severity"] == "warning"
    assert payload["requires_action"] is False
    assert payload["metadata"]["service"] == "universal-agent-gateway"


def test_notifier_payload_flapping_backoff_escalates():
    module = _load_notifier()
    payload = module._build_payload(_ns(event="flapping_backoff", escalated=True, restart_count=7))
    assert payload["severity"] == "error"
    assert payload["requires_action"] is True
    assert "flapping" in payload["title"].lower()
    assert payload["metadata"]["event"] == "flapping_backoff"
