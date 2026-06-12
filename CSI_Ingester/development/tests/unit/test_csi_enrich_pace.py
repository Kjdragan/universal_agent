"""Tests for the CSI enrich slow-burn pacing knob (_csi_pace).

The enrich crons are already sequential; this knob just spaces their per-event
ZAI/GLM calls so a run trickles instead of bursting and tripping the Fair-Usage
rate limit. We verify resolution (default / env override / invalid / clamp) and
that the sleep is a no-op when disabled.
"""

from __future__ import annotations

from pathlib import Path
import sys

script_dir = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(script_dir))

import _csi_pace  # noqa: E402


def test_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("CSI_ENRICH_PACE_SECONDS", raising=False)
    assert _csi_pace.resolve_enrich_pace_seconds() == _csi_pace.ENRICH_PACE_SECONDS_DEFAULT
    assert _csi_pace.resolve_enrich_pace_seconds("") == _csi_pace.ENRICH_PACE_SECONDS_DEFAULT
    assert _csi_pace.ENRICH_PACE_SECONDS_DEFAULT > 0  # default is a real delay


def test_env_override_wins():
    assert _csi_pace.resolve_enrich_pace_seconds("12") == 12.0
    assert _csi_pace.resolve_enrich_pace_seconds("  90.5  ") == 90.5


def test_invalid_falls_back_to_default():
    assert _csi_pace.resolve_enrich_pace_seconds("not-a-number") == _csi_pace.ENRICH_PACE_SECONDS_DEFAULT


def test_negative_clamps_to_zero():
    assert _csi_pace.resolve_enrich_pace_seconds("-5") == 0.0


def test_zero_disables():
    assert _csi_pace.resolve_enrich_pace_seconds("0") == 0.0


def test_pace_sleep_is_noop_when_not_positive(monkeypatch):
    calls = []
    monkeypatch.setattr(_csi_pace.time, "sleep", lambda s: calls.append(s))
    _csi_pace.pace_sleep(0)
    _csi_pace.pace_sleep(-3)
    assert calls == []
    _csi_pace.pace_sleep(7)
    assert calls == [7]
