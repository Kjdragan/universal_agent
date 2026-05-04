"""Phase 1 — Operator-driven Mission Control refresh.

Covers the new `force_refresh_async` path on the sweeper and the
in-memory job worker that backs the
`POST /api/v1/dashboard/mission-control/refresh` endpoint.

The existing background sweeper has gating windows (floor/ceiling) so
tier-1 + tier-2 LLM passes don't burn quota on every tick. The operator
"Refresh Mission Control" button needs the OPPOSITE: skip the gating,
run cards then readout NOW, surface progress.

These tests pin:
  - `force_refresh_async` always invokes tier-1 then tier-2, even when
    gating would normally skip them.
  - The progress callback emits ordered phase transitions.
  - Tier-1 failure short-circuits and reports `phase="cards"`.
  - Tier-2 failure preserves tier-1 cards and reports `phase="readout"`.
  - The gateway worker (`_run_mc_refresh_job`) walks the job dict
    through queued → cards_running → readout_running → completed.
  - Worker exceptions land as `failed` in the job dict.

Endpoint-level (POST/GET 410) integration tests are kept lightweight
here and rely on the worker tests for behavioral coverage.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from universal_agent.services.mission_control_db import open_store
from universal_agent.services.mission_control_intelligence_sweeper import (
    MissionControlSweeper,
    SweeperConfig,
)


# ── Helpers ────────────────────────────────────────────────────────────


def _seed_meta(db_path: Path, *, tile_id: str, age_seconds: int) -> None:
    """Seed a __tier1_meta__ or __tier2_meta__ row that would normally
    cause `_tier1_skip_reason` / `_tier2_skip_reason` to skip.

    Used by the gating-bypass tests: if `force_refresh_async` honors
    these rows, it would skip — proving the bypass works requires
    seeding values that WOULD skip in the natural path.
    """
    iso = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
    conn = open_store(db_path)
    try:
        conn.execute(
            """
            INSERT INTO mission_control_tile_states (
                tile_id, current_state, state_since, last_signature,
                last_checked_at, current_annotation
            ) VALUES (?, 'unknown', ?, 'seed-sig', ?, 'seed')
            """,
            (tile_id, iso, iso),
        )
    finally:
        conn.close()


# ── force_refresh_async — gating bypass + ordering ─────────────────────


@pytest.mark.asyncio
async def test_force_refresh_runs_tier1_then_tier2_ignoring_gating(
    tmp_path: Path, monkeypatch
):
    """Even when both tier-1 and tier-2 would normally be skipped (recent
    meta rows inside floor windows), force_refresh_async must invoke
    both LLM passes. This is the whole point of the operator button."""
    db_path = tmp_path / "mc.db"
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(db_path))

    # Seed both meta rows VERY recent so the natural gating would skip.
    _seed_meta(db_path, tile_id="__tier1_meta__", age_seconds=5)
    _seed_meta(db_path, tile_id="__tier2_meta__", age_seconds=5)

    sequence: list[str] = []

    async def fake_tier1(self_, result, *, force=False):
        assert force is True, "force flag must be True on operator path"
        sequence.append("tier1")
        result.tier1_synthesized = True

    async def fake_tier2(self_, result, *, force=False):
        assert force is True, "force flag must be True on operator path"
        sequence.append("tier2")
        result.tier2_synthesized = True

    monkeypatch.setattr(MissionControlSweeper, "_run_tier1_async", fake_tier1)
    monkeypatch.setattr(MissionControlSweeper, "_run_tier2_async", fake_tier2)

    sweeper = MissionControlSweeper(SweeperConfig())
    summary = await sweeper.force_refresh_async()

    assert sequence == ["tier1", "tier2"]
    assert summary["tier1_synthesized"] is True
    assert summary["tier2_synthesized"] is True
    assert summary["status"] == "completed"


@pytest.mark.asyncio
async def test_force_refresh_progress_callback_emits_phase_transitions(
    tmp_path: Path, monkeypatch
):
    """The callback must observe cards_running → readout_running →
    completed in that order. The dashboard relies on this ordering for
    the progress pill."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    async def fake_tier1(self_, result, *, force=False):
        result.tier1_synthesized = True

    async def fake_tier2(self_, result, *, force=False):
        result.tier2_synthesized = True

    monkeypatch.setattr(MissionControlSweeper, "_run_tier1_async", fake_tier1)
    monkeypatch.setattr(MissionControlSweeper, "_run_tier2_async", fake_tier2)

    events: list[tuple[str, dict[str, Any]]] = []

    def on_progress(phase: str, payload: dict[str, Any]) -> None:
        events.append((phase, dict(payload)))

    sweeper = MissionControlSweeper(SweeperConfig())
    await sweeper.force_refresh_async(progress=on_progress)

    phases = [phase for phase, _ in events]
    assert phases == ["cards_running", "readout_running", "completed"]
    # Each event must carry an ISO timestamp for UI rendering.
    for _, payload in events:
        assert "at" in payload and payload["at"]


@pytest.mark.asyncio
async def test_force_refresh_records_failure_on_tier1_exception(
    tmp_path: Path, monkeypatch
):
    """If tier-1 raises, the readout step must NOT run (cards drive
    readout — synthesizing on a failed card refresh would re-stale the
    brief). The callback receives a single failed event with
    phase="cards"."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    async def broken_tier1(self_, result, *, force=False):
        raise RuntimeError("tier1 boom")

    tier2_calls = {"n": 0}

    async def fake_tier2(self_, result, *, force=False):
        tier2_calls["n"] += 1
        result.tier2_synthesized = True

    monkeypatch.setattr(MissionControlSweeper, "_run_tier1_async", broken_tier1)
    monkeypatch.setattr(MissionControlSweeper, "_run_tier2_async", fake_tier2)

    events: list[tuple[str, dict[str, Any]]] = []

    sweeper = MissionControlSweeper(SweeperConfig())
    summary = await sweeper.force_refresh_async(progress=lambda p, d: events.append((p, d)))

    assert tier2_calls["n"] == 0  # readout must not run on cards failure
    assert summary["status"] == "failed"
    assert summary["failed_phase"] == "cards"
    assert "tier1 boom" in summary["error"]
    failed = [e for e in events if e[0] == "failed"]
    assert len(failed) == 1
    assert failed[0][1]["phase"] == "cards"


@pytest.mark.asyncio
async def test_force_refresh_records_failure_on_tier2_exception(
    tmp_path: Path, monkeypatch
):
    """Tier-1 succeeds, tier-2 raises: cards remain persisted (we never
    rollback tier-1), the callback reports a failed phase="readout"."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    async def fake_tier1(self_, result, *, force=False):
        result.tier1_synthesized = True

    async def broken_tier2(self_, result, *, force=False):
        raise RuntimeError("tier2 kaboom")

    monkeypatch.setattr(MissionControlSweeper, "_run_tier1_async", fake_tier1)
    monkeypatch.setattr(MissionControlSweeper, "_run_tier2_async", broken_tier2)

    events: list[tuple[str, dict[str, Any]]] = []

    sweeper = MissionControlSweeper(SweeperConfig())
    summary = await sweeper.force_refresh_async(progress=lambda p, d: events.append((p, d)))

    assert summary["status"] == "failed"
    assert summary["failed_phase"] == "readout"
    assert summary["tier1_synthesized"] is True
    assert "tier2 kaboom" in summary["error"]
    phases = [phase for phase, _ in events]
    # cards_running fired (tier-1 ran), then readout_running started, then failed
    assert phases[0] == "cards_running"
    assert "failed" in phases
    failed = [e for e in events if e[0] == "failed"]
    assert failed[0][1]["phase"] == "readout"


# ── Gateway worker (`_run_mc_refresh_job`) lifecycle ───────────────────


@pytest.mark.asyncio
async def test_run_mc_refresh_job_walks_lifecycle(tmp_path: Path, monkeypatch):
    """The worker must update the in-memory job dict through every
    phase: queued → cards_running → readout_running → completed. The
    dashboard polls this dict via the GET endpoint."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    from universal_agent import gateway_server

    # Snapshot dict so test doesn't leak across runs.
    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {})

    # Stub the sweeper getter to a shim with our forced-refresh impl.
    seen_phases: list[str] = []

    class FakeSweeper:
        async def force_refresh_async(self, *, progress=None):
            for phase in ("cards_running", "readout_running"):
                seen_phases.append(phase)
                if progress:
                    progress(phase, {"at": "iso"})
                await asyncio.sleep(0)
            if progress:
                progress("completed", {"at": "iso"})
            return {
                "status": "completed",
                "tier1_synthesized": True,
                "tier2_synthesized": True,
                "readout_id": "fake_readout_42",
                "card_count_changed": 3,
            }

    monkeypatch.setattr(gateway_server, "_get_mission_control_sweeper", lambda: FakeSweeper())

    job_id = "test-job-1"
    gateway_server._mc_refresh_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": "2026-05-04T12:00:00+00:00",
    }

    await gateway_server._run_mc_refresh_job(job_id)

    final = gateway_server._mc_refresh_jobs[job_id]
    assert final["status"] == "completed"
    assert final["readout_id"] == "fake_readout_42"
    assert final["card_count_changed"] == 3
    # Progress timestamps are recorded (UI shows phase_started_at).
    assert "phase_started_at" in final or "completed_at" in final


@pytest.mark.asyncio
async def test_run_mc_refresh_job_records_failure(tmp_path: Path, monkeypatch):
    """When `force_refresh_async` returns failed, the job dict must
    capture status=failed plus the error string and failed_phase so the
    UI can show a useful retry banner."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {})

    class FailingSweeper:
        async def force_refresh_async(self, *, progress=None):
            if progress:
                progress("cards_running", {"at": "iso"})
                progress("failed", {"phase": "cards", "error": "boom", "at": "iso"})
            return {
                "status": "failed",
                "failed_phase": "cards",
                "error": "boom",
                "tier1_synthesized": False,
                "tier2_synthesized": False,
            }

    monkeypatch.setattr(gateway_server, "_get_mission_control_sweeper", lambda: FailingSweeper())

    job_id = "test-job-2"
    gateway_server._mc_refresh_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": "2026-05-04T12:00:00+00:00",
    }

    await gateway_server._run_mc_refresh_job(job_id)

    final = gateway_server._mc_refresh_jobs[job_id]
    assert final["status"] == "failed"
    assert final["failed_phase"] == "cards"
    assert "boom" in final["error"]


@pytest.mark.asyncio
async def test_run_mc_refresh_job_catches_unexpected_exception(
    tmp_path: Path, monkeypatch
):
    """If the sweeper's force_refresh_async raises (instead of returning
    failed), the worker must still mark the job as failed — not leak
    the exception to the asyncio task wrapper where it would silently
    log."""
    monkeypatch.setenv("UA_MISSION_CONTROL_INTEL_DB_PATH", str(tmp_path / "mc.db"))

    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {})

    class CrashingSweeper:
        async def force_refresh_async(self, *, progress=None):
            raise RuntimeError("totally unexpected")

    monkeypatch.setattr(gateway_server, "_get_mission_control_sweeper", lambda: CrashingSweeper())

    job_id = "test-job-3"
    gateway_server._mc_refresh_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "created_at": "2026-05-04T12:00:00+00:00",
    }

    await gateway_server._run_mc_refresh_job(job_id)
    final = gateway_server._mc_refresh_jobs[job_id]
    assert final["status"] == "failed"
    assert "totally unexpected" in final["error"]


# ── In-memory job dict retention ───────────────────────────────────────


def test_remember_mc_refresh_job_caps_retention(monkeypatch):
    """The dict must not grow unbounded. We cap to last 32 jobs FIFO so
    operators can still look back at recent refresh history without
    leaking memory on long-lived gateway processes."""
    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {})

    for i in range(40):
        gateway_server._remember_mc_refresh_job({
            "job_id": f"job-{i:03d}",
            "status": "completed",
            "created_at": f"2026-05-04T12:00:{i:02d}+00:00",
        })

    jobs = gateway_server._mc_refresh_jobs
    assert len(jobs) == 32
    # Oldest 8 must be evicted.
    assert "job-000" not in jobs
    assert "job-007" not in jobs
    assert "job-008" in jobs
    assert "job-039" in jobs


# ── Endpoint deprecation contract ──────────────────────────────────────


def test_old_chief_of_staff_refresh_returns_410(monkeypatch, tmp_path):
    """The synchronous endpoint is deprecated. POSTs must return 410
    with a Link header pointing at the new async endpoint, so any stale
    caller (or curl-using operator) gets a clear migration signal."""
    from fastapi.testclient import TestClient

    from universal_agent import gateway_server

    # The endpoint itself is pure (no DB writes) so we can hit the app
    # directly without the heavy ops_api fixture.
    client = TestClient(gateway_server.app)
    resp = client.post("/api/v1/dashboard/chief-of-staff/refresh")
    assert resp.status_code == 410
    body = resp.json()
    assert body["error"] == "deprecated"
    assert body["new_endpoint"] == "/api/v1/dashboard/mission-control/refresh"
    link = resp.headers.get("link", "")
    assert "/api/v1/dashboard/mission-control/refresh" in link
    assert 'rel="successor-version"' in link


def test_post_mission_control_refresh_returns_202(monkeypatch, tmp_path):
    """The new async endpoint must return 202 Accepted with a job_id
    and status=queued. The frontend uses the job_id to poll."""
    from fastapi.testclient import TestClient

    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {})

    # Stub the worker so the endpoint can return without spinning a
    # real Anthropic call. We just want to assert the contract.
    started: list[str] = []

    async def fake_worker(job_id: str) -> None:
        started.append(job_id)

    monkeypatch.setattr(gateway_server, "_run_mc_refresh_job", fake_worker)

    client = TestClient(gateway_server.app)
    resp = client.post("/api/v1/dashboard/mission-control/refresh")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert len(body["job_id"]) >= 8  # uuid hex prefix


def test_get_mission_control_refresh_unknown_returns_404(monkeypatch):
    from fastapi.testclient import TestClient

    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {})
    client = TestClient(gateway_server.app)
    resp = client.get("/api/v1/dashboard/mission-control/refresh/does-not-exist")
    assert resp.status_code == 404


def test_get_mission_control_refresh_returns_job(monkeypatch):
    from fastapi.testclient import TestClient

    from universal_agent import gateway_server

    monkeypatch.setattr(gateway_server, "_mc_refresh_jobs", {
        "abc123": {
            "job_id": "abc123",
            "status": "cards_running",
            "created_at": "2026-05-04T12:00:00+00:00",
            "phase_started_at": "2026-05-04T12:00:01+00:00",
        }
    })
    client = TestClient(gateway_server.app)
    resp = client.get("/api/v1/dashboard/mission-control/refresh/abc123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "abc123"
    assert body["status"] == "cards_running"
