from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from universal_agent.durable.db import connect_runtime_db, get_vp_db_path
from universal_agent.durable.migrations import ensure_schema
from universal_agent.durable.state import list_vp_missions
from universal_agent.tools.vp_orchestration import (
    _vp_dispatch_mission_impl,
    _vp_read_result_artifacts_impl,
    _vp_wait_mission_impl,
)
from universal_agent.vp.dispatcher import (
    MissionDispatchRequest,
    cancel_mission,
    dispatch_mission_with_retry,
)
from universal_agent.vp.clients.base import MissionOutcome, VpClient
from universal_agent.vp.worker_loop import VpWorkerLoop


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_vp_tools_and_worker_produce_and_read_real_artifacts(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))

    dispatch = _payload(
        asyncio.run(
            _vp_dispatch_mission_impl(
                {
                    "vp_id": "vp.general.primary",
                    "objective": "Create a markdown work product",
                    "mission_type": "general_task",
                    "constraints": {"target_path": str(tmp_path / "general_target")},
                    "idempotency_key": "integration-vp-artifacts-1",
                }
            )
        )
    )
    assert dispatch["ok"] is True
    mission_id = dispatch["mission_id"]

    class _WorkerClient(VpClient):
        async def run_mission(self, *, mission, workspace_root):
            mission_dir = workspace_root / str(mission["mission_id"])
            wp = mission_dir / "work_products"
            wp.mkdir(parents=True, exist_ok=True)
            (wp / "summary.md").write_text("# Integration Summary\n\nWorker completed.\n", encoding="utf-8")
            return MissionOutcome(
                status="completed",
                result_ref=f"workspace://{mission_dir}",
                payload={"artifact_relpath": "work_products/summary.md"},
            )

    conn = connect_runtime_db(get_vp_db_path())
    try:
        ensure_schema(conn)
        loop = VpWorkerLoop(
            conn=conn,
            vp_id="vp.general.primary",
            workspace_base=tmp_path,
            poll_interval_seconds=1,
            lease_ttl_seconds=60,
        )
        loop._client = _WorkerClient()  # type: ignore[assignment]
        asyncio.run(loop._tick())
    finally:
        conn.close()

    waited = _payload(
        asyncio.run(
            _vp_wait_mission_impl(
                {"mission_id": mission_id, "timeout_seconds": 5, "poll_seconds": 1}
            )
        )
    )
    assert waited["ok"] is True
    assert waited["timed_out"] is False
    assert waited["mission"]["status"] == "completed"

    artifacts = _payload(
        asyncio.run(
            _vp_read_result_artifacts_impl(
                {"mission_id": mission_id, "max_files": 10, "max_bytes": 100_000}
            )
        )
    )
    assert artifacts["ok"] is True
    assert any(item["path"] == "work_products/summary.md" for item in artifacts["artifacts"])


def test_vp_tools_and_worker_support_coder_lane_with_handoff_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    handoff_root = (tmp_path / "vp_handoff").resolve()
    handoff_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("UA_VP_HANDOFF_ROOT", str(handoff_root))

    dispatch = _payload(
        asyncio.run(
            _vp_dispatch_mission_impl(
                {
                    "vp_id": "vp.coder.primary",
                    "objective": "Create a coder-lane markdown artifact",
                    "mission_type": "coder_task",
                    "constraints": {"target_path": str(handoff_root / "project_alpha")},
                    "idempotency_key": "integration-vp-coder-artifacts-1",
                }
            )
        )
    )
    assert dispatch["ok"] is True
    mission_id = dispatch["mission_id"]
    assert dispatch["vp_id"] == "vp.coder.primary"

    class _CoderWorkerClient(VpClient):
        async def run_mission(self, *, mission, workspace_root):
            mission_dir = workspace_root / str(mission["mission_id"])
            wp = mission_dir / "work_products"
            wp.mkdir(parents=True, exist_ok=True)
            (wp / "coder_report.md").write_text("# Coder Result\n\nCompleted.\n", encoding="utf-8")
            return MissionOutcome(
                status="completed",
                result_ref=f"workspace://{mission_dir}",
                payload={"artifact_relpath": "work_products/coder_report.md"},
            )

    conn = connect_runtime_db(get_vp_db_path())
    try:
        ensure_schema(conn)
        loop = VpWorkerLoop(
            conn=conn,
            vp_id="vp.coder.primary",
            workspace_base=tmp_path,
            poll_interval_seconds=1,
            lease_ttl_seconds=60,
        )
        loop._client = _CoderWorkerClient()  # type: ignore[assignment]
        asyncio.run(loop._tick())
    finally:
        conn.close()

    waited = _payload(
        asyncio.run(
            _vp_wait_mission_impl(
                {"mission_id": mission_id, "timeout_seconds": 5, "poll_seconds": 1}
            )
        )
    )
    assert waited["ok"] is True
    assert waited["timed_out"] is False
    assert waited["mission"]["status"] == "completed"
    assert waited["mission"]["vp_id"] == "vp.coder.primary"

    artifacts = _payload(
        asyncio.run(
            _vp_read_result_artifacts_impl(
                {"mission_id": mission_id, "max_files": 10, "max_bytes": 100_000}
            )
        )
    )
    assert artifacts["ok"] is True
    assert any(item["path"] == "work_products/coder_report.md" for item in artifacts["artifacts"])


def test_vp_dispatch_list_cancel_concurrency_with_worker_polling(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("UA_VP_DB_PATH", str((tmp_path / "vp_state.db").resolve()))
    db_path = get_vp_db_path()
    bootstrap_conn = connect_runtime_db(db_path)
    try:
        ensure_schema(bootstrap_conn)
    finally:
        bootstrap_conn.close()

    mission_ids: list[str] = []
    mission_ids_lock = threading.Lock()
    errors: list[str] = []
    errors_lock = threading.Lock()
    stop_worker = threading.Event()

    class _ConcurrentWorkerClient(VpClient):
        async def run_mission(self, *, mission, workspace_root):
            mission_id = str(mission.get("mission_id") or "mission")
            mission_dir = workspace_root / mission_id
            wp = mission_dir / "work_products"
            wp.mkdir(parents=True, exist_ok=True)
            (wp / "result.txt").write_text("ok\n", encoding="utf-8")
            await asyncio.sleep(0.01)
            return MissionOutcome(
                status="completed",
                result_ref=f"workspace://{mission_dir}",
                payload={"artifact_relpath": "work_products/result.txt"},
            )

    def _record_error(exc: Exception) -> None:
        with errors_lock:
            errors.append(f"{type(exc).__name__}: {exc}")

    def _worker_poller() -> None:
        conn = connect_runtime_db(db_path)
        try:
            ensure_schema(conn)
            loop = VpWorkerLoop(
                conn=conn,
                vp_id="vp.general.primary",
                workspace_base=tmp_path,
                poll_interval_seconds=0.2,
                lease_ttl_seconds=60,
            )
            loop._client = _ConcurrentWorkerClient()  # type: ignore[assignment]

            async def _run() -> None:
                while not stop_worker.is_set():
                    await loop._tick()
                    await asyncio.sleep(0.005)

            asyncio.run(_run())
        except Exception as exc:
            _record_error(exc)
        finally:
            conn.close()

    def _dispatch_batch(batch_label: str, count: int) -> None:
        conn = connect_runtime_db(db_path)
        try:
            ensure_schema(conn)
            for idx in range(count):
                try:
                    request = MissionDispatchRequest(
                        vp_id="vp.general.primary",
                        mission_type="general_task",
                        objective=f"concurrency objective {batch_label}-{idx}",
                        constraints={},
                        budget={},
                        idempotency_key=f"concurrency-{batch_label}-{idx}",
                        source_session_id="integration.concurrent",
                        source_turn_id=f"turn-{batch_label}-{idx}",
                        reply_mode="async",
                        priority=100,
                    )
                    row = dispatch_mission_with_retry(conn=conn, request=request)
                    with mission_ids_lock:
                        mission_ids.append(str(row["mission_id"]))
                except Exception as exc:
                    _record_error(exc)
        finally:
            conn.close()

    def _list_batch(iterations: int) -> None:
        conn = connect_runtime_db(db_path)
        try:
            ensure_schema(conn)
            for _ in range(iterations):
                try:
                    _ = list_vp_missions(
                        conn=conn,
                        vp_id="vp.general.primary",
                        statuses=None,
                        limit=500,
                    )
                except Exception as exc:
                    _record_error(exc)
        finally:
            conn.close()

    def _cancel_batch(iterations: int) -> None:
        conn = connect_runtime_db(db_path)
        seen: set[str] = set()
        try:
            ensure_schema(conn)
            for _ in range(iterations):
                try:
                    with mission_ids_lock:
                        targets = [item for item in mission_ids if item not in seen][:4]
                    for mission_id in targets:
                        cancel_mission(conn=conn, mission_id=mission_id, reason="integration_concurrency")
                        seen.add(mission_id)
                    time.sleep(0.005)
                except Exception as exc:
                    _record_error(exc)
        finally:
            conn.close()

    worker_thread = threading.Thread(target=_worker_poller, daemon=True)
    worker_thread.start()
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(_dispatch_batch, "A", 20),
                pool.submit(_dispatch_batch, "B", 20),
                pool.submit(_dispatch_batch, "C", 20),
                pool.submit(_list_batch, 80),
                pool.submit(_list_batch, 80),
                pool.submit(_cancel_batch, 120),
            ]
            for future in futures:
                future.result(timeout=60)
    finally:
        stop_worker.set()
        worker_thread.join(timeout=10)

    assert not errors, f"Unexpected concurrent VP operation errors: {errors}"
    with mission_ids_lock:
        assert len(mission_ids) >= 50

    verify_conn = connect_runtime_db(db_path)
    try:
        ensure_schema(verify_conn)
        rows = list_vp_missions(verify_conn, vp_id="vp.general.primary", statuses=None, limit=1000)
    finally:
        verify_conn.close()

    status_values = {str(row["status"]) for row in rows}
    assert status_values <= {"queued", "running", "completed", "failed", "cancelled"}
    assert status_values & {"completed", "cancelled", "running", "queued"}
