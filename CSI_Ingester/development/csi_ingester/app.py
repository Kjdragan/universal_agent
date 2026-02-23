"""CSI Ingester FastAPI app."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from csi_ingester.config import CSIConfig, load_config
from csi_ingester.logging import configure_logging
from csi_ingester.metrics import MetricsRegistry
from csi_ingester.service import CSIService
from csi_ingester.store import analysis_tasks as analysis_task_store
from csi_ingester.store.sqlite import connect, ensure_schema

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="CSI Ingester", version="0.1.0")

_metrics = MetricsRegistry()
_config: CSIConfig | None = None
_db_conn = None
_service: CSIService | None = None


class AnalysisTaskCreateRequest(BaseModel):
    request_type: str = Field(..., min_length=1, max_length=160)
    priority: int = Field(default=50, ge=0, le=1000)
    request_source: str = Field(default="ua", min_length=1, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class AnalysisTaskCancelRequest(BaseModel):
    reason: str = Field(default="canceled", max_length=4000)


@app.on_event("startup")
async def _startup() -> None:
    global _config, _db_conn, _service
    _config = load_config()
    db_path = _config.db_path if _config else Path("var/csi.db")
    _db_conn = connect(db_path)
    ensure_schema(_db_conn)
    if _config is not None:
        _service = CSIService(config=_config, conn=_db_conn, metrics=_metrics)
        await _service.start()
    logger.info("CSI started db_path=%s", db_path)


@app.on_event("shutdown")
async def _shutdown() -> None:
    global _db_conn, _service
    if _service is not None:
        await _service.stop()
        _service = None
    if _db_conn is not None:
        _db_conn.close()
        _db_conn = None


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    _metrics.inc("csi.healthz.calls")
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict[str, object]:
    _metrics.inc("csi.readyz.calls")
    if _config is None or _db_conn is None or _service is None:
        return {"ready": False}
    return {"ready": True, "instance_id": _config.instance_id}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    return _metrics.render_prometheus()


@app.post("/analysis/tasks")
async def create_analysis_task(payload: AnalysisTaskCreateRequest) -> dict[str, Any]:
    if _db_conn is None:
        raise HTTPException(status_code=503, detail="db_not_ready")
    try:
        task = analysis_task_store.create_task(
            _db_conn,
            request_type=payload.request_type,
            payload=payload.payload,
            priority=payload.priority,
            request_source=payload.request_source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "task": task}


@app.get("/analysis/tasks")
async def list_analysis_tasks(
    status: str = Query(default="", max_length=64),
    request_type: str = Query(default="", max_length=160),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if _db_conn is None:
        raise HTTPException(status_code=503, detail="db_not_ready")
    tasks = analysis_task_store.list_tasks(
        _db_conn,
        status=status,
        request_type=request_type,
        limit=limit,
        offset=offset,
    )
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


@app.get("/analysis/tasks/{task_id}")
async def get_analysis_task(task_id: str) -> dict[str, Any]:
    if _db_conn is None:
        raise HTTPException(status_code=503, detail="db_not_ready")
    task = analysis_task_store.get_task(_db_conn, task_id.strip())
    if task is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    return {"ok": True, "task": task}


@app.post("/analysis/tasks/{task_id}/cancel")
async def cancel_analysis_task(task_id: str, payload: AnalysisTaskCancelRequest) -> dict[str, Any]:
    if _db_conn is None:
        raise HTTPException(status_code=503, detail="db_not_ready")
    task = analysis_task_store.cancel_task(
        _db_conn,
        task_id=task_id.strip(),
        reason=payload.reason,
    )
    if task is None:
        raise HTTPException(status_code=404, detail="task_not_found")
    return {"ok": True, "task": task}
