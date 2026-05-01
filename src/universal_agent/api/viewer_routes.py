"""FastAPI router for the centralized three-panel viewer (Track B).

Two endpoints:

  POST /api/viewer/resolve   — any identity hints → SessionViewTarget
  GET  /api/viewer/hydrate   — full hydration payload for the new route

Producers (Task Hub, Sessions, Calendar, Proactive, Chat) call /resolve
to get the canonical viewer target including the `viewer_href` they
should navigate to. The viewer route then calls /hydrate on mount and
on each readiness poll.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from universal_agent.viewer import (
    SessionViewTarget,
    hydrate,
    resolve_session_view_target,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Models ───────────────────────────────────────────────────────────────────


class ResolveBody(BaseModel):
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    workspace_dir: Optional[str] = None
    workspace_name: Optional[str] = None


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/api/viewer/resolve")
async def viewer_resolve(body: ResolveBody) -> JSONResponse:
    """Resolve any combination of identity hints to a canonical SessionViewTarget.

    UI producers MUST call this and use the returned `viewer_href` rather
    than building viewer URLs locally — that's the contract that ends
    the per-producer URL-building drift.
    """
    if not any(
        (
            body.session_id,
            body.run_id,
            body.workspace_dir,
            body.workspace_name,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of session_id, run_id, workspace_dir, workspace_name is required.",
        )

    target = resolve_session_view_target(
        session_id=body.session_id,
        run_id=body.run_id,
        workspace_dir=body.workspace_dir,
        workspace_name=body.workspace_name,
    )
    if target is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": {
                    "code": "viewer_target_not_found",
                    "message": "Could not resolve a viewer target from the given hints.",
                    "hints": body.model_dump(exclude_none=True),
                }
            },
        )
    return JSONResponse(content=target.to_dict())


@router.get("/api/viewer/hydrate")
async def viewer_hydrate(
    target_kind: str = Query(..., pattern="^(run|session)$"),
    target_id: str = Query(..., min_length=1),
    history_limit: int = Query(500, ge=1, le=5000),
    logs_limit: int = Query(1000, ge=1, le=10000),
) -> JSONResponse:
    """Server-assembled three-panel payload for the viewer route.

    Resolves first (so callers can hand us anchor IDs without separately
    POSTing /resolve), then hydrates. The viewer route polls this every
    2s while readiness=pending; once ready it falls back to the existing
    chat websocket for live updates.
    """
    if target_kind == "run":
        target = resolve_session_view_target(run_id=target_id)
    else:
        target = resolve_session_view_target(session_id=target_id)

    if target is None:
        return JSONResponse(
            status_code=404,
            content={
                "detail": {
                    "code": "viewer_target_not_found",
                    "message": f"Unknown {target_kind}: {target_id}",
                }
            },
        )

    result = hydrate(target, history_limit=history_limit, logs_limit=logs_limit)
    return JSONResponse(content=result.to_dict())


@router.get("/api/viewer/health")
async def viewer_health() -> dict[str, Any]:
    """Sanity check + version probe for the viewer subsystem."""
    return {
        "ok": True,
        "subsystem": "viewer",
        "endpoints": [
            "POST /api/viewer/resolve",
            "GET /api/viewer/hydrate",
        ],
    }
