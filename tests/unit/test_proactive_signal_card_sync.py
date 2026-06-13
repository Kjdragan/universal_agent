"""Tests for the autonomous proactive-signal-card generation tick.

Covers the script's dormancy gate (active-window default + UA_PROACTIVE_CARD_SYNC_24_7
escape hatch) and pins the systemd unit + installer + remote_deploy wiring so the
timer can't silently lose its deploy install.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from universal_agent.scripts import proactive_signal_card_sync as tick

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SYSTEMD_DIR = _REPO_ROOT / "deployment" / "systemd"
_TIMER = _SYSTEMD_DIR / "universal-agent-proactive-signal-card-sync.timer"
_SERVICE = _SYSTEMD_DIR / "universal-agent-proactive-signal-card-sync.service"
_INSTALLER = _REPO_ROOT / "scripts" / "install_vps_proactive_signal_card_sync_timer.sh"
_REMOTE_DEPLOY = _REPO_ROOT / "scripts" / "deploy" / "remote_deploy.sh"


# --- dormancy gate -----------------------------------------------------------

def test_dormant_window_skips_generation(monkeypatch):
    """Default mode, dormant: should_run() is False → main() no-ops (no DB work)."""
    monkeypatch.delenv("UA_PROACTIVE_CARD_SYNC_24_7", raising=False)
    gen = MagicMock()
    with patch.object(tick, "should_run", return_value=False) as sr, patch.object(
        tick, "connect_runtime_db"
    ) as conn_mock, patch.object(tick, "generate_signal_cards", gen):
        rc = tick.main()

    assert rc == 0
    # The mode passed must be the windowed default (not "always").
    assert sr.call_args.kwargs.get("mode") == "dormancy_aware"
    conn_mock.assert_not_called()
    gen.assert_not_called()


def test_active_window_runs_generation(monkeypatch):
    """Default mode, active: should_run() True → main() generates cards."""
    monkeypatch.delenv("UA_PROACTIVE_CARD_SYNC_24_7", raising=False)
    gen = MagicMock(return_value={"youtube": 3, "discord": 1, "expired": 0})
    fake_conn = MagicMock()
    with patch.object(tick, "should_run", return_value=True), patch.object(
        tick, "connect_runtime_db", return_value=fake_conn
    ), patch.object(tick, "get_activity_db_path", return_value="/x/activity.db"), patch.object(
        tick, "generate_signal_cards", gen
    ):
        rc = tick.main()

    assert rc == 0
    gen.assert_called_once()
    fake_conn.close.assert_called_once()


def test_24_7_escape_hatch_uses_always_mode(monkeypatch):
    """UA_PROACTIVE_CARD_SYNC_24_7=true → should_run is called with mode='always'."""
    monkeypatch.setenv("UA_PROACTIVE_CARD_SYNC_24_7", "true")
    with patch.object(tick, "should_run", return_value=True) as sr, patch.object(
        tick, "connect_runtime_db", return_value=MagicMock()
    ), patch.object(tick, "get_activity_db_path", return_value="/x/activity.db"), patch.object(
        tick, "generate_signal_cards", return_value={"youtube": 0, "discord": 0, "expired": 0}
    ):
        tick.main()

    assert sr.call_args.kwargs.get("mode") == "always"


def test_generation_error_returns_nonzero(monkeypatch):
    """A generation exception → main() logs and returns 1 (so the unit records a
    failure) and still closes the connection."""
    monkeypatch.setenv("UA_PROACTIVE_CARD_SYNC_24_7", "true")
    fake_conn = MagicMock()
    with patch.object(tick, "should_run", return_value=True), patch.object(
        tick, "connect_runtime_db", return_value=fake_conn
    ), patch.object(tick, "get_activity_db_path", return_value="/x/activity.db"), patch.object(
        tick, "generate_signal_cards", side_effect=RuntimeError("boom")
    ):
        rc = tick.main()

    assert rc == 1
    fake_conn.close.assert_called_once()


# --- deploy wiring (drift guard) ---------------------------------------------

def test_timer_is_runtime_gated_full_day():
    text = _TIMER.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 00..23:25:00 America/Chicago" in text
    assert "Persistent=true" in text
    assert "RandomizedDelaySec=" in text  # jitter so it doesn't cluster on the hour


def test_service_runs_the_tick_module():
    text = _SERVICE.read_text(encoding="utf-8")
    assert "universal_agent.scripts.proactive_signal_card_sync" in text
    assert "Type=oneshot" in text


def test_installer_covers_units_and_arms_timer():
    text = _INSTALLER.read_text(encoding="utf-8")
    assert "universal-agent-proactive-signal-card-sync.service" in text
    assert "universal-agent-proactive-signal-card-sync.timer" in text
    assert "systemctl enable --now" in text


def test_remote_deploy_wires_the_installer():
    text = _REMOTE_DEPLOY.read_text(encoding="utf-8")
    assert "install_vps_proactive_signal_card_sync_timer.sh" in text
