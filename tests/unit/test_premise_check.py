"""Tests for the verify-against-reality helpers (top-9 handoff, task 8).

The two seeded known-bad premises are REAL incidents: completion narratives
cited a `/api/v1/hackernews/refresh` "producer" (the path only appears in
prose — no route registration exists) and the retired `csi_analytics`
trend-timer "producer" (no system_job registration, no systemd unit).
"""

from __future__ import annotations

import json

from universal_agent.services.premise_check import (
    NO_CHECK_DEFINED,
    UNVERIFIED,
    VERIFIED,
    verify_code_claim,
    verify_mission_against_reality,
)


# ── seeded known-bad premises ───────────────────────────────────────────────


def test_hackernews_refresh_route_flags_unverified():
    v = verify_code_claim("route:/api/v1/hackernews/refresh")
    assert v.status == UNVERIFIED
    assert v.kind == "route"


def test_csi_analytics_trend_timer_flags_unverified():
    v = verify_code_claim("cron:csi_analytics")
    assert v.status == UNVERIFIED
    assert v.kind == "cron"


# ── known-good claims must verify (no false alarms) ─────────────────────────


def test_real_route_verifies():
    v = verify_code_claim("route:/api/v1/ops/proactive_health")
    assert v.status == VERIFIED
    assert "gateway_server" in v.evidence


def test_real_cron_registration_verifies():
    v = verify_code_claim("cron:paper_to_podcast_daily")
    assert v.status == VERIFIED


def test_real_systemd_timer_verifies():
    v = verify_code_claim("timer:vp-coder-regenerable-reap")
    assert v.status == VERIFIED
    assert "systemd" in v.evidence


def test_real_symbol_verifies():
    v = verify_code_claim(
        "symbol:src/universal_agent/task_hub.py::get_pending_review_tasks"
    )
    assert v.status == VERIFIED
    assert "task_hub.py" in v.evidence


def test_missing_symbol_flags_unverified():
    v = verify_code_claim(
        "symbol:src/universal_agent/task_hub.py::this_function_does_not_exist_xyz"
    )
    assert v.status == UNVERIFIED


def test_prose_mention_does_not_vouch_for_a_route():
    """The hackernews path appears in csi_source_liveness docstrings — prose
    must not count as a route registration (that is exactly how the false
    premise survived)."""
    v = verify_code_claim("route:/api/v1/hackernews/refresh")
    assert v.status == UNVERIFIED


def test_checker_never_raises_on_garbage():
    for garbage in ("", None, "route:", "symbol:::", "cron:", 42):
        v = verify_code_claim(garbage)  # type: ignore[arg-type]
        assert v.status in {VERIFIED, UNVERIFIED}


# ── per-mission-type completion gate ────────────────────────────────────────


def test_tutorial_build_with_real_manifest_verifies(tmp_path):
    ws = tmp_path / "demo-proactive-x"
    ws.mkdir()
    (ws / "manifest.json").write_text(json.dumps({"demo_id": "x"}))
    result = verify_mission_against_reality(
        source_kind="tutorial_build",
        finalize_result={"ok": True, "workspace_dir": str(ws)},
    )
    assert result["status"] == VERIFIED
    assert result["check"] == "demo_workspace_manifest_exists"


def test_tutorial_build_missing_manifest_flags_unverified(tmp_path):
    ws = tmp_path / "demo-proactive-y"
    ws.mkdir()  # no manifest.json
    result = verify_mission_against_reality(
        source_kind="tutorial_build",
        finalize_result={"ok": True, "workspace_dir": str(ws)},
    )
    assert result["status"] == UNVERIFIED


def test_tutorial_build_without_workspace_flags_unverified():
    result = verify_mission_against_reality(
        source_kind="directed_build", finalize_result={"ok": False}
    )
    assert result["status"] == UNVERIFIED


def test_unknown_mission_type_fails_open():
    result = verify_mission_against_reality(
        source_kind="intel_brief", finalize_result=None
    )
    assert result["status"] == NO_CHECK_DEFINED
    assert "fail-open" in result["evidence"]
