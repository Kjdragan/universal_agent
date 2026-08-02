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
    restart attempt every cycle) and appends each restart to restart_log.

    `${ACTIVE_STATE-inactive}` (no colon) only falls back to "inactive" when
    ACTIVE_STATE is truly unset — an explicit empty string (ACTIVE_STATE="")
    is passed through as an empty `is-active` result, which is exactly the
    probe-failure scenario (timeout / dbus hiccup / systemctl erroring before
    it prints anything) the fix in check_service must no longer treat as
    "down"."""
    fake = tmp_path / "fake_systemctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        '  is-active) echo "${ACTIVE_STATE-inactive}"; exit "${ACTIVE_STATE_RC:-0}";;\n'
        '  is-enabled) echo "${ENABLED_STATE:-enabled}"; exit 0;;\n'
        '  restart) echo "$2" >> "$RESTART_LOG"; exit "${RESTART_RC:-0}";;\n'
        "  reset-failed) exit 0;;\n"
        "  *) exit 0;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


def _run_cycle(
    tmp_path: Path,
    state_dir: Path,
    fake_systemctl: Path,
    restart_log: Path,
    max_per_hour: int,
    enabled_state: str = "enabled",
    active_state: str = "inactive",
    probe_fail_threshold: int = 3,
):
    env = dict(os.environ)
    env.update(
        {
            "UA_WATCHDOG_SYSTEMCTL_BIN": str(fake_systemctl),
            "UA_WATCHDOG_STATE_DIR": str(state_dir),
            "UA_WATCHDOG_SERVICE_SPECS": "fakesvc||",
            "UA_WATCHDOG_NOTIFY_ENABLED": "0",
            "UA_WATCHDOG_POST_RESTART_SETTLE_SECONDS": "0",
            "UA_WATCHDOG_MAX_RESTARTS_PER_HOUR": str(max_per_hour),
            "UA_WATCHDOG_PROBE_FAIL_THRESHOLD": str(probe_fail_threshold),
            "RESTART_LOG": str(restart_log),
            "ENABLED_STATE": enabled_state,
            "ACTIVE_STATE": active_state,
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


def test_watchdog_skips_transient_states(tmp_path):
    """A unit caught mid-(re)start in a TRANSIENT state (`deactivating`,
    `activating`, `reloading`) is left alone — not force-restarted. This was the
    root cause of the recurring "[WARNING] Watchdog restarted" emails: the 30s
    watchdog kept catching the autonomous-runtime/sweeper workers during their
    slow graceful drain (`deactivating`) on deploy restarts and redundantly
    restarting them (reason was always `inactive:deactivating`)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    restart_log.write_text("", encoding="utf-8")
    fake = _fake_systemctl(tmp_path, restart_log)

    for transient in ("deactivating", "activating", "reloading"):
        proc = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour=6, active_state=transient)
        assert proc.returncode == 0, proc.stderr
        restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert restarts == [], f"transient state {transient!r} must NOT trigger a restart, got {restarts}"
        assert f"state=transient:{transient} action=skip" in proc.stdout

    # Sanity: a genuinely-down unit (`failed`) IS still restarted — the dead-unit
    # backstop is preserved, only the transient flap is suppressed.
    proc = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour=6, active_state="failed")
    assert proc.returncode == 0, proc.stderr
    restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert restarts == ["fakesvc"], f"failed unit must be restarted, got {restarts}"


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


def test_watchdog_empty_probe_result_never_restarts_but_inactive_still_does(tmp_path):
    """T8(a) regression test. An EMPTY `systemctl is-active` result (probe
    timeout / dbus hiccup / systemctl erroring before printing anything) must
    never be read as "down" — that bug fired 7 false restarts on 08-01/08-02
    against services PID 1 proves were running continuously, one of which
    bounced a healthy production service. A genuinely non-empty "inactive"
    result must still restart as before (the fix removes the false inference,
    not the backstop)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    restart_log.write_text("", encoding="utf-8")
    fake = _fake_systemctl(tmp_path, restart_log)

    # Several consecutive empty-probe cycles: never restart.
    for _ in range(5):
        proc = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour=6, active_state="")
        assert proc.returncode == 0, proc.stderr
        assert "state=probe_unavailable" in proc.stdout
        assert "action=skip_restart" in proc.stdout

    restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert restarts == [], f"empty is-active result must NEVER trigger a restart, got {restarts}"

    # The consecutive-probe-failure counter is persisted alongside the other
    # per-service state files, separate from the health-check fail counter.
    probe_fail_file = state_dir / "fakesvc.probefail"
    assert probe_fail_file.exists()
    assert int(probe_fail_file.read_text(encoding="utf-8").strip()) == 5

    # Sanity: the SAME service, when is-active genuinely reports "inactive"
    # (non-empty), IS restarted — the empty-result skip is the only behavior
    # change, not a general refusal to restart.
    proc = _run_cycle(tmp_path, state_dir, fake, restart_log, max_per_hour=6, active_state="inactive")
    assert proc.returncode == 0, proc.stderr
    restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert restarts == ["fakesvc"], f"non-empty 'inactive' result must still be restarted, got {restarts}"

    # And the probe-fail counter resets once the probe starts answering again.
    assert int(probe_fail_file.read_text(encoding="utf-8").strip()) == 0


def test_watchdog_probe_unavailable_escalates_after_threshold(tmp_path):
    """T8(a): a probe that stays empty for PROBE_FAIL_THRESHOLD consecutive
    cycles escalates a notification (the backstop for a genuinely wedged
    systemd/dbus), even though no restart is ever attempted."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    restart_log = tmp_path / "restarts.log"
    restart_log.write_text("", encoding="utf-8")
    fake = _fake_systemctl(tmp_path, restart_log)

    procs = []
    for _ in range(3):
        procs.append(
            _run_cycle(
                tmp_path, state_dir, fake, restart_log, max_per_hour=6, active_state="", probe_fail_threshold=3
            )
        )
    for proc in procs:
        assert proc.returncode == 0, proc.stderr

    # Below threshold (cycles 1-2): counted, but no restart, and no restart
    # was ever attempted for any cycle in this scenario.
    assert "consecutive_probe_failures=1" in procs[0].stdout
    assert "consecutive_probe_failures=2" in procs[1].stdout
    # At threshold (cycle 3): still no restart (a probe failure is never
    # evidence of a down service, regardless of how many cycles it persists).
    assert "consecutive_probe_failures=3" in procs[2].stdout
    assert "threshold=3" in procs[2].stdout
    restarts = [ln for ln in restart_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert restarts == [], f"probe_unavailable must never trigger a restart, got {restarts}"


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


def test_notifier_payload_restart_escalated_by_real_flapping_flag():
    """T8(b) regression test: a SUCCESSFUL restart that happens to be the
    one-shot escalated attempt allowed once the flap cooldown elapses
    (escalated=True passed as the real computed `$flapping` flag, not a
    hardcoded literal) still renders the "(flapping)" wording correctly."""
    module = _load_notifier()
    payload = module._build_payload(_ns(event="restart", escalated=True, post_state="active", restart_count=4))
    assert payload["severity"] == "error"
    assert payload["requires_action"] is True
    assert "flapping" in payload["title"].lower()
    assert payload["metadata"]["event"] == "restart"
    assert payload["metadata"]["escalated"] is True


def test_notifier_payload_restart_command_failed_is_its_own_branch():
    """T8(b) regression test. Before the fix, a failed `systemctl restart`
    COMMAND was reported via the flapping branch with a hardcoded
    escalated=1/post_state="failed" — rendering "restarted (flapping)" next
    to a self-contradicting "1x in the last 60m" body for services that were
    actually healthy (4 phantom ERRORs). `restart_command_failed` must be its
    own branch with its own title/severity wording, independent of the
    flapping flag, and must carry the REAL observed post-attempt state."""
    module = _load_notifier()

    # Not flapping (a one-off restart-command failure on an otherwise healthy
    # service, restart_count=1 — the exact shape of the reported bug).
    payload = module._build_payload(
        _ns(
            event="restart_command_failed",
            escalated=False,
            post_state="restart_command_failed:active",
            restart_count=1,
            window_seconds=3600,
        )
    )
    assert payload["severity"] == "error"
    assert payload["requires_action"] is True
    assert "flapping" not in payload["title"].lower()
    assert "failed" in payload["title"].lower()
    assert "restart command" in payload["message"].lower() or "systemctl restart" in payload["message"]
    assert "restart_command_failed:active" in payload["message"]
    assert payload["metadata"]["event"] == "restart_command_failed"
    assert payload["metadata"]["post_state"] == "restart_command_failed:active"

    # escalated=True (genuinely flapping AND the restart command failed) still
    # renders via the restart_command_failed branch, not the flapping wording.
    payload_escalated = module._build_payload(
        _ns(event="restart_command_failed", escalated=True, post_state="restart_command_failed:failed", restart_count=5)
    )
    assert payload_escalated["severity"] == "error"
    assert payload_escalated["requires_action"] is True
    assert "flapping" not in payload_escalated["title"].lower()


def test_notifier_payload_probe_unavailable_never_implies_restart():
    """T8(a) regression test: an empty is-active probe result must never be
    reported through the restart/flapping vocabulary — it's a distinct event
    where no restart was attempted at all."""
    module = _load_notifier()
    payload = module._build_payload(
        _ns(
            event="probe_unavailable",
            reason="is-active probe returned empty",
            post_state="probe_unavailable",
            escalated=False,
            restart_count=3,
        )
    )
    assert payload["severity"] == "error"
    assert payload["requires_action"] is True
    assert "restarted" not in payload["title"].lower()
    assert "flapping" not in payload["title"].lower()
    assert "probe" in payload["title"].lower()
    assert payload["metadata"]["event"] == "probe_unavailable"
