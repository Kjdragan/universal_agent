"""CSI Ingester FastAPI app."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from csi_ingester.config import CSIConfig, load_config
from csi_ingester.logging import configure_logging
from csi_ingester.metrics import MetricsRegistry
from csi_ingester.service import CSIService
from csi_ingester.store.sqlite import connect, ensure_schema

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title="CSI Ingester", version="0.1.0")

_metrics = MetricsRegistry()
_config: CSIConfig | None = None
_db_conn = None
_service: CSIService | None = None


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
