"""Read-only HN snapshot endpoints. Mounted on /api/v1/hackernews."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from universal_agent.services.hackernews_article_reader import fetch_article
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


@router.get("/article")
async def get_article(
    url: str = Query(..., description="HTTP(S) URL to extract reader-mode content from"),
) -> dict[str, Any]:
    """Reader-mode extraction for the dashboard preview overlay.

    Many HN-linked sites refuse to be embedded in an iframe via
    ``X-Frame-Options`` / ``Content-Security-Policy: frame-ancestors``.
    This endpoint fetches the URL server-side and returns extracted
    title + byline + lead-image + markdown body so the modal can
    render a clean reader view instead of a blank gray panel.

    Always returns 200 with a structured payload — the ``ok`` flag
    tells the frontend whether to render the reader or fall back to
    the iframe / new-tab affordance.
    """
    try:
        result = await asyncio.to_thread(fetch_article, url)
    except Exception as exc:  # noqa: BLE001 — never let the helper crash the route
        logger.exception("hackernews article reader crashed for url=%s", url)
        return {
            "ok": False,
            "error": f"reader_crashed: {type(exc).__name__}: {exc}",
            "host": "",
            "source_url": url,
        }
    return result
