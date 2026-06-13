"""Tests for the deterministic wiki-rescue driver (decision execution)."""

from __future__ import annotations

import asyncio

from universal_agent.services import wiki_rescue_driver as drv
import universal_agent.tools.vp_orchestration as vpo


def _run(coro):
    return asyncio.run(coro)


def _call(mission_type="proactive_wiki", failure_mode="timeout", status="failed", mission_id="m1"):
    return _run(
        drv.maybe_rescue_failed_wiki_mission(
            mission_id=mission_id,
            mission_type=mission_type,
            failure_mode=failure_mode,
            status=status,
        )
    )


def _patch_dispatch(monkeypatch):
    """Replace the two async rescue impls with recording stubs; return the log."""
    calls: list[tuple[str, dict]] = []

    async def fake_redispatch(args):
        calls.append(("redispatch", dict(args)))
        return {"ok": True}

    async def fake_escalate(args):
        calls.append(("escalate", dict(args)))
        return {"ok": True}

    monkeypatch.setattr(vpo, "_vp_dispatch_mission_redispatch_fresh_impl", fake_redispatch)
    monkeypatch.setattr(vpo, "_escalate_vp_failure_to_operator_impl", fake_escalate)
    return calls


# --- gating ----------------------------------------------------------------

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("UA_WIKI_RESCUE_ENABLED", raising=False)
    assert _call() is None


def test_out_of_scope_mission_type_skipped(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    assert _call(mission_type="tutorial_build") is None


def test_non_terminal_status_skipped(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    assert _call(status="running") is None


def test_no_failure_task_skips(monkeypatch):
    # operator_cancel / skipped surfacing => no vp_failure task => nothing to do.
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: None)
    calls = _patch_dispatch(monkeypatch)
    assert _call() is None
    assert calls == []


# --- execution paths -------------------------------------------------------

def test_transient_retries_on_atlas_no_override(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 1, "failure_mode": "timeout"})
    monkeypatch.setattr(drv, "_cody_available", lambda: True)
    calls = _patch_dispatch(monkeypatch)
    out = _call()
    assert out["action"] == "retry_atlas"
    assert len(calls) == 1 and calls[0][0] == "redispatch"
    args = calls[0][1]
    assert "override_vp_id" not in args  # ATLAS retry stays on the original VP
    assert args["rescue_action"] == "retry_atlas"
    assert args["mission_id"] == "m1"


def test_exhausted_hands_to_cody_with_override(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 3, "failure_mode": "timeout"})
    monkeypatch.setattr(drv, "_cody_available", lambda: True)
    calls = _patch_dispatch(monkeypatch)
    out = _call()
    assert out["action"] == "handoff_cody"
    args = calls[0][1]
    assert args.get("override_vp_id")  # redirected to Cody
    assert args["rescue_action"] == "handoff_cody"


def test_exhausted_cody_busy_falls_back_to_atlas(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 3, "failure_mode": "timeout"})
    monkeypatch.setattr(drv, "_cody_available", lambda: False)
    calls = _patch_dispatch(monkeypatch)
    out = _call()
    assert out["action"] == "retry_atlas"
    assert "override_vp_id" not in calls[0][1]


def test_budget_exhausted_escalates(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 4, "failure_mode": "timeout"})
    monkeypatch.setattr(drv, "_cody_available", lambda: True)
    calls = _patch_dispatch(monkeypatch)
    out = _call()
    assert out["action"] == "escalate"
    assert calls[0][0] == "escalate"
    assert calls[0][1]["mission_id"] == "m1"
    assert calls[0][1]["why_escalating"]


def test_structural_failure_hands_to_cody(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 1, "failure_mode": "workspace_guard"})
    monkeypatch.setattr(drv, "_cody_available", lambda: True)
    calls = _patch_dispatch(monkeypatch)
    out = _call()
    assert out["action"] == "handoff_cody"
    assert calls[0][1].get("override_vp_id")


def test_dispatch_error_is_swallowed(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 1, "failure_mode": "timeout"})
    monkeypatch.setattr(drv, "_cody_available", lambda: True)

    async def boom(args):
        raise RuntimeError("dispatch exploded")

    monkeypatch.setattr(vpo, "_vp_dispatch_mission_redispatch_fresh_impl", boom)
    out = _call()  # must not raise
    assert out["action"] == "retry_atlas"
    assert "error" in out


def test_dry_run_reports_decision_without_dispatch(monkeypatch):
    monkeypatch.setenv("UA_WIKI_RESCUE_ENABLED", "1")
    monkeypatch.setenv("UA_WIKI_RESCUE_DRY_RUN", "1")
    monkeypatch.setattr(drv, "_load_failure_meta", lambda mid: {"failure_count": 1, "failure_mode": "timeout"})
    monkeypatch.setattr(drv, "_cody_available", lambda: True)
    calls = _patch_dispatch(monkeypatch)
    out = _call()
    assert out["dry_run"] is True
    assert out["action"] == "retry_atlas"  # the verdict it WOULD have taken
    assert calls == []  # ...but nothing was dispatched


# --- schedule_wiki_rescue (sync-safe bridge for the stale reconciler) -------

def _patch_rescue_recorder(monkeypatch):
    """Replace maybe_rescue_failed_wiki_mission with a recording coroutine."""
    recorded: list[dict] = []

    async def fake_rescue(**kwargs):
        recorded.append(kwargs)
        return {"action": "noop"}

    monkeypatch.setattr(drv, "maybe_rescue_failed_wiki_mission", fake_rescue)
    return recorded


def test_schedule_without_running_loop_runs_inline(monkeypatch):
    recorded = _patch_rescue_recorder(monkeypatch)
    drv.schedule_wiki_rescue(
        mission_id="m-recon", mission_type="proactive_wiki",
        failure_mode="stale_claim_expired", status="failed",
    )
    assert len(recorded) == 1
    assert recorded[0]["mission_id"] == "m-recon"
    assert recorded[0]["failure_mode"] == "stale_claim_expired"


def test_schedule_with_running_loop_creates_task(monkeypatch):
    recorded = _patch_rescue_recorder(monkeypatch)

    async def main():
        # Sync call from inside a running loop (the reconciler's situation).
        drv.schedule_wiki_rescue(
            mission_id="m-loop", mission_type="proactive_wiki",
            failure_mode="stale_reconcile", status="failed",
        )
        assert recorded == []  # not run yet — scheduled as a task
        await asyncio.sleep(0)  # yield so the task runs
        await asyncio.sleep(0)

    asyncio.run(main())
    assert len(recorded) == 1
    assert recorded[0]["mission_id"] == "m-loop"


def test_schedule_swallows_rescue_errors(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("rescue exploded")

    monkeypatch.setattr(drv, "maybe_rescue_failed_wiki_mission", boom)
    # No-loop path: must not raise.
    drv.schedule_wiki_rescue(
        mission_id="m-err", mission_type="proactive_wiki",
        failure_mode="timeout", status="failed",
    )

    async def main():
        # Running-loop path: task error must be consumed by the done-callback.
        drv.schedule_wiki_rescue(
            mission_id="m-err2", mission_type="proactive_wiki",
            failure_mode="timeout", status="failed",
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(main())  # must not raise
