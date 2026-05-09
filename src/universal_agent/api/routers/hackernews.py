"""Read-only HN snapshot endpoints. Mounted on /api/v1/hackernews."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from universal_agent.services.hackernews_snapshot_service import (
    build_snapshot,
    read_latest,
    write_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/hackernews", tags=["hackernews"])


def _age_seconds(generated_at: str) -> float:
    try:
        ts = datetime.fromisoformat(generated_at)
    except (TypeError, ValueError):
        return float("inf")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


@router.get("/snapshot")
def get_snapshot() -> dict[str, Any]:
    snap = read_latest()
    if snap is None:
        raise HTTPException(
            status_code=503,
            detail="no snapshot yet — cron has not run",
        )
    return snap


@router.get("/health")
def health() -> dict[str, Any]:
    snap = read_latest()
    if snap is None:
        return {"status": "cold", "age_seconds": None, "errors": []}
    generated_at = snap.get("meta", {}).get("generated_at", "")
    age = _age_seconds(generated_at)
    if age <= 2700:
        status = "ok"
    elif age <= 7200:
        status = "stale"
    else:
        status = "error"
    return {
        "status": status,
        "age_seconds": int(age) if age != float("inf") else None,
        "errors": snap.get("meta", {}).get("errors", []),
    }


@router.post("/refresh")
def refresh_now() -> dict[str, Any]:
    try:
        snap = build_snapshot()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        logger.exception("hackernews refresh crashed")
        raise HTTPException(status_code=500, detail=f"refresh failed: {exc}")
    write_snapshot(snap)
    return {"ok": True, "errors": snap.get("meta", {}).get("errors", [])}
